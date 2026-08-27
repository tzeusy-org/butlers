# Core Connector Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the canonical connector heartbeat liveness verdict from the dashboard DTO layer into the existing core liveness policy module without changing thresholds or caller-visible behavior.

**Architecture:** `butlers.core.liveness` is the stable in-process policy seam already shared by core and roster code. API routers, Switchboard API code, and QA discovery will import `derive_liveness` directly from that seam; `butlers.api.models.connector` will return to containing Pydantic response models only, and the broad `butlers.api.models` facade will stop exporting the retired symbol.

**Tech Stack:** Python 3.12, pytest, Ruff, stdlib `datetime`

## Global Constraints

- Work only in `/home/tze/GitHub/butlers/.worktrees/quality-core-liveness` on `refactor/core-connector-liveness`.
- Do not mutate Beads, push, open a PR, or contact GitHub.
- Preserve the exact `None`, future-clock-skew, five-minute, and fifteen-minute verdict behavior.
- Add no dependency, alias, compatibility shim, API payload change, schema change, or OpenSpec/RFC change.
- Keep `.venv` local to this worktree and never replace it with a symlink.
- Baseline evidence: `uv run pytest tests/core/test_liveness.py tests/api/test_connector_liveness.py tests/core/qa/test_infra_state.py -q --tb=short` passed `70` tests before edits.

---

### Task 1: Retarget connector liveness tests to the core seam

**Files:**
- Move: `tests/api/test_connector_liveness.py` to `tests/core/test_connector_liveness.py`
- Modify: `tests/core/test_connector_liveness.py`

**Interfaces:**
- Consumes: current connector liveness behavior from `butlers.api.models.connector.derive_liveness`
- Produces: regression tests that require `butlers.core.liveness.derive_liveness(last_heartbeat_at: datetime | None) -> str`

- [ ] **Step 1: Move the behavior test to the owning layer and make it address the wished-for core API**

  Run `git mv tests/api/test_connector_liveness.py tests/core/test_connector_liveness.py`, then replace the API import with a module import so collection succeeds before the function exists:

  ```python
  from butlers.core import liveness
  ```

  Update each assertion to call `liveness.derive_liveness(...)`.

- [ ] **Step 2: Run one focused test to verify RED**

  Run:

  ```bash
  uv run pytest tests/core/test_connector_liveness.py::TestDeriveLivenessClockSkew::test_none_heartbeat_is_offline -q --tb=short
  ```

  Expected: `FAIL` with `AttributeError` because `butlers.core.liveness` does not yet define `derive_liveness`.

### Task 2: Move the unchanged policy into core

**Files:**
- Modify: `src/butlers/core/liveness.py`
- Modify: `src/butlers/api/models/connector.py`
- Test: `tests/core/test_connector_liveness.py`

**Interfaces:**
- Consumes: `datetime | None`
- Produces: `derive_liveness(last_heartbeat_at: datetime | None) -> str`, returning only `"online"`, `"stale"`, or `"offline"`

- [ ] **Step 1: Add the minimal production implementation at the core seam**

  Move this implementation unchanged into `src/butlers/core/liveness.py` and remove it from `src/butlers/api/models/connector.py`:

  ```python
  def derive_liveness(last_heartbeat_at: datetime | None) -> str:
      """Derive liveness status from last heartbeat timestamp.

      Liveness thresholds (from docs/connectors/heartbeat.md):
      - online: heartbeat within last 5 minutes
      - stale: heartbeat between 5-15 minutes ago
      - offline: no heartbeat for 15+ minutes or never seen

      A future-dated heartbeat (more than 5 minutes ahead of server clock) is
      treated as offline rather than online to avoid false-healthy reports under
      clock skew.
      """
      if last_heartbeat_at is None:
          return "offline"

      import datetime as dt

      now = dt.datetime.now(dt.UTC)
      age = (now - last_heartbeat_at).total_seconds()

      if age < -300:  # more than 5 minutes in the future — clock skew
          return "offline"
      elif age <= 300:  # 5 minutes
          return "online"
      elif age <= 900:  # 15 minutes
          return "stale"
      else:
          return "offline"
  ```

  Preserve the existing explanatory threshold and clock-skew docstring content when moving it.

- [ ] **Step 2: Verify GREEN at the new seam**

  Run:

  ```bash
  uv run pytest tests/core/test_connector_liveness.py tests/core/test_liveness.py -q --tb=short
  ```

  Expected: all connector and generic core liveness tests pass.

### Task 3: Redirect every consumer and retire the API facade export

**Files:**
- Modify: `src/butlers/api/models/__init__.py`
- Modify: `src/butlers/api/routers/calendar_workspace.py`
- Modify: `src/butlers/api/routers/google_health.py`
- Modify: `src/butlers/api/routers/ingestion_connectors.py`
- Modify: `src/butlers/api/routers/owntracks.py`
- Modify: `src/butlers/core/qa/sources/infra_state.py`
- Modify: `roster/switchboard/api/router.py`
- Modify: `tests/api/test_owntracks_liveness.py`

**Interfaces:**
- Consumes: `butlers.core.liveness.derive_liveness`
- Produces: zero production or test references to `butlers.api.models.connector.derive_liveness`, with no compatibility alias

- [ ] **Step 1: Update production imports**

  Replace direct imports from `butlers.api.models.connector` with imports from `butlers.core.liveness`. Preserve existing local aliases such as `_liveness` where callers already use them.

- [ ] **Step 2: Update ownership comments and docs in touched code**

  Replace comments and docstrings that name `butlers.api.models.connector` with `butlers.core.liveness`. Do not rewrite unrelated copy.

- [ ] **Step 3: Remove the facade export**

  Remove `derive_liveness` from both the connector import group and `__all__` in `src/butlers/api/models/__init__.py`; do not leave an alias.

- [ ] **Step 4: Prove the retired dependency is gone**

  Run:

  ```bash
  rg -n "butlers\.api\.models\.connector.*derive_liveness|derive_liveness.*butlers\.api\.models\.connector" src/butlers roster tests
  ```

  Expected: zero matches.

### Task 4: Focused verification and review

**Files:**
- Verify all files changed by Tasks 1-3

**Interfaces:**
- Consumes: the completed core seam and redirected callers
- Produces: merge-review evidence without pushing or contacting GitHub

- [ ] **Step 1: Run targeted behavioral tests**

  ```bash
  uv run pytest \
    tests/core/test_liveness.py \
    tests/core/test_connector_liveness.py \
    tests/core/qa/test_infra_state.py \
    tests/api/test_owntracks_liveness.py \
    tests/api/test_connector_archive_candidates.py \
    tests/api/test_api_google_health.py \
    tests/api/test_google_health_router.py \
    tests/api/test_calendar_workspace.py \
    tests/api/test_ingestion_connectors_available.py \
    tests/api/test_switchboard.py \
    -q --tb=short
  ```

- [ ] **Step 2: Run Ruff on the exact changed Python files**

  ```bash
  uv run ruff check \
    src/butlers/core/liveness.py \
    src/butlers/api/models/connector.py \
    src/butlers/api/models/__init__.py \
    src/butlers/api/routers/calendar_workspace.py \
    src/butlers/api/routers/google_health.py \
    src/butlers/api/routers/ingestion_connectors.py \
    src/butlers/api/routers/owntracks.py \
    src/butlers/core/qa/sources/infra_state.py \
    roster/switchboard/api/router.py \
    tests/core/test_connector_liveness.py \
    tests/api/test_owntracks_liveness.py
  uv run ruff format --check \
    src/butlers/core/liveness.py \
    src/butlers/api/models/connector.py \
    src/butlers/api/models/__init__.py \
    src/butlers/api/routers/calendar_workspace.py \
    src/butlers/api/routers/google_health.py \
    src/butlers/api/routers/ingestion_connectors.py \
    src/butlers/api/routers/owntracks.py \
    src/butlers/core/qa/sources/infra_state.py \
    roster/switchboard/api/router.py \
    tests/core/test_connector_liveness.py \
    tests/api/test_owntracks_liveness.py
  ```

  Expected: both commands exit `0`.

- [ ] **Step 3: Review the patch**

  Inspect `git diff --check`, `git diff --stat`, and `git diff`. Confirm the function body and thresholds are unchanged, dependencies now point API/roster/QA to core, no alias remains, comments name the correct owner, and no user-visible/API/spec behavior changed.

- [ ] **Step 4: Commit the scoped implementation**

  ```bash
  git add src/butlers/core/liveness.py src/butlers/api/models/connector.py \
    src/butlers/api/models/__init__.py src/butlers/api/routers/calendar_workspace.py \
    src/butlers/api/routers/google_health.py src/butlers/api/routers/ingestion_connectors.py \
    src/butlers/api/routers/owntracks.py src/butlers/core/qa/sources/infra_state.py \
    roster/switchboard/api/router.py tests/core/test_connector_liveness.py \
    tests/api/test_owntracks_liveness.py
  git commit -m "refactor(core): own connector liveness policy"
  ```
