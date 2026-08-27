# OAuth Module Token Payload Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Calendar and Contacts module OAuth clients validate successful token responses through the repository's canonical payload validator before mutating cached token state.

**Architecture:** Keep each module's existing HTTP, retry, revocation, and callback orchestration intact. Replace only the duplicated successful-response field extraction with `validate_oauth_token_payload()`, translate its validation error into the module's fixed local exception type, and delete the now-unused permissive expiry coercers.

**Tech Stack:** Python 3.12, httpx, pytest/pytest-asyncio, Ruff, existing `butlers.oauth_token_payload` validation.

## Global Constraints

- Work only in `/home/tze/GitHub/butlers/.worktrees/quality-oauth-payload` on `fix/oauth-module-token-payload`.
- Do not mutate Beads, push, open a PR, contact GitHub, or edit another worktree.
- Never expose token payload values in exceptions, logs, assertions, or committed fixtures; all test material remains synthetic.
- Validate the complete response before assigning access token or expiry so rejection cannot partially mutate state.
- Preserve Calendar retry, `invalid_grant` revocation, and `on_token_refreshed` callback behavior; preserve Contacts retry behavior.
- Use targeted tests during development. Do not run the full repository suite for this scoped implementation.

---

### Task 1: Add regression coverage for both missing validator consumers

**Files:**
- Modify: `tests/connectors/test_oauth_token_payload_sites.py`
- Modify: `tests/modules/test_module_calendar.py`

**Interfaces:**
- Consumes: `_VALID_PAYLOAD`, `_REJECTED_PAYLOADS`, and `_token_client()` already defined in the canonical token-site test.
- Produces: behavioral coverage for `butlers.modules.calendar._GoogleOAuthClient` and `butlers.modules.contacts.sync._GoogleOAuthClient`.

- [ ] **Step 1: Import both module clients and domain exceptions under unambiguous aliases**

```python
from butlers.modules.calendar import (
    CalendarTokenRefreshError,
    _GoogleOAuthClient as CalendarGoogleOAuthClient,
    _GoogleOAuthCredentials as CalendarGoogleOAuthCredentials,
)
from butlers.modules.contacts.sync import (
    ContactsTokenRefreshError,
    _GoogleOAuthClient as ContactsGoogleOAuthClient,
    _GoogleOAuthCredentials as ContactsGoogleOAuthCredentials,
)
```

- [ ] **Step 2: Add Calendar module success and malformed-response tests**

Create a client with synthetic credentials and `_token_client(payload)`. Seed `_access_token` and `_access_token_expires_at` with stale sentinel values. The valid test asserts the fresh token, a future expiry, and one `on_token_refreshed` call. The parameterized malformed test asserts:

```python
with pytest.raises(CalendarTokenRefreshError) as exc_info:
    await oauth.get_access_token(force_refresh=True)

assert str(exc_info.value) == "Google OAuth token endpoint returned an invalid token payload"
assert oauth._access_token == _STALE_ACCESS_TOKEN
assert oauth._access_token_expires_at == stale_expiry
on_token_refreshed.assert_not_awaited()
```

- [ ] **Step 3: Add equivalent Contacts module success and malformed-response tests**

Use the Contacts aliases and assert the exact same fixed local message through `ContactsTokenRefreshError`, along with unchanged stale token/expiry state after every rejected payload.

- [ ] **Step 4: Remove the obsolete coercion-helper unit test and import**

Delete `_coerce_expires_in_seconds` from the imports and delete `TestGoogleHelpers.test_coerce_expires_in`; malformed expiry behavior now belongs to the canonical token-site contract.

- [ ] **Step 5: Run the new site tests to prove RED**

Run:

```bash
uv run pytest tests/connectors/test_oauth_token_payload_sites.py \
  -k 'calendar_module or contacts_module' -q
```

Expected: valid-payload cases pass; malformed `expires_in` cases fail because current module clients accept/default those payloads instead of raising the domain exception.

---

### Task 2: Route both clients through canonical validation

**Files:**
- Modify: `src/butlers/modules/calendar.py`
- Modify: `src/butlers/modules/contacts/sync.py`

**Interfaces:**
- Consumes: `validate_oauth_token_payload(payload: object) -> OAuthTokenPayload` and `OAuthTokenValidationError` from `butlers.oauth_token_payload`.
- Produces: unchanged valid token-cache behavior and fixed local `CalendarTokenRefreshError` / `ContactsTokenRefreshError` mapping for malformed success payloads.

- [ ] **Step 1: Import the canonical validator and validation exception in Calendar**

```python
from butlers.oauth_token_payload import OAuthTokenValidationError, validate_oauth_token_payload
```

- [ ] **Step 2: Validate Calendar's decoded JSON before state assignment**

Retain the existing invalid-JSON branch, then replace manual access-token extraction and expiry coercion with:

```python
try:
    token = validate_oauth_token_payload(payload)
except OAuthTokenValidationError as exc:
    raise CalendarTokenRefreshError(
        "Google OAuth token endpoint returned an invalid token payload"
    ) from exc

refresh_ttl_seconds = max(token.expires_in - 60, 30)
self._access_token = token.access_token
self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=refresh_ttl_seconds)
```

Delete Calendar's `_coerce_expires_in_seconds` helper. Do not change retry, revocation, or callback blocks.

- [ ] **Step 3: Apply the same parsing boundary in Contacts**

Import the same shared symbols, translate to `ContactsTokenRefreshError` using the same fixed local message, use `token.access_token` / `token.expires_in`, and delete Contacts' `_coerce_expires_in_seconds`. Do not change Contacts retry behavior.

- [ ] **Step 4: Run the RED scope to prove GREEN**

Run:

```bash
uv run pytest tests/connectors/test_oauth_token_payload_sites.py \
  -k 'calendar_module or contacts_module' -q
```

Expected: all selected cases pass with no warnings or leaked payload text.

- [ ] **Step 5: Run the complete canonical token-site test**

Run:

```bash
uv run pytest tests/config/test_oauth_token_payload.py \
  tests/connectors/test_oauth_token_payload_sites.py -q
```

Expected: all canonical validator and extraction-site cases pass.

---

### Task 3: Reconcile inventory, verify affected modules, and self-review

**Files:**
- Modify if needed for truthful wording: `tests/connectors/test_oauth_token_payload_sites.py`
- Review: all changed files

**Interfaces:**
- Consumes: the production and regression changes from Tasks 1-2.
- Produces: a truthful extraction-site inventory and scoped verification evidence.

- [ ] **Step 1: Re-grep successful token consumers**

Run:

```bash
rg -n --glob '*.py' \
  'access_token|expires_in|validate_oauth_token_payload|parse_spotify_token_response' \
  src/butlers/api src/butlers/connectors src/butlers/modules \
  | rg 'response\.json|payload|get\(|\[|validate_|parse_'
```

Inspect every successful token-endpoint extraction hit. Update the canonical test docstring/site numbering to describe all seven in-file Google refresh consumers plus the separately covered callback, without claiming unverified coverage.

- [ ] **Step 2: Run affected module tests**

Run:

```bash
uv run pytest tests/modules/test_module_calendar.py::TestGoogleHelpers \
  tests/modules/test_module_contacts.py -q
```

Expected: Calendar's retry/revocation helper tests and the Contacts module contract remain green.

- [ ] **Step 3: Run Ruff on every changed Python file**

Run:

```bash
uv run ruff check \
  src/butlers/modules/calendar.py \
  src/butlers/modules/contacts/sync.py \
  tests/connectors/test_oauth_token_payload_sites.py \
  tests/modules/test_module_calendar.py
uv run ruff format --check \
  src/butlers/modules/calendar.py \
  src/butlers/modules/contacts/sync.py \
  tests/connectors/test_oauth_token_payload_sites.py \
  tests/modules/test_module_calendar.py
```

Expected: both commands exit zero. If format check fails, run `uv run ruff format` on only those files and repeat both checks.

- [ ] **Step 4: Self-review the final diff**

Confirm from `git diff --check` and `git diff` that:

- validation completes before either cached field changes;
- exceptions contain only fixed local text and preserve the validation cause;
- invalid JSON, non-2xx retry, Calendar revocation, and callback control flow are unchanged;
- both permissive coercers and their direct test/import are gone;
- no unrelated files or documentation were changed.

- [ ] **Step 5: Commit the scoped implementation**

```bash
git add src/butlers/modules/calendar.py \
  src/butlers/modules/contacts/sync.py \
  tests/connectors/test_oauth_token_payload_sites.py \
  tests/modules/test_module_calendar.py
git commit -m "fix(oauth): validate module token responses"
```

