# Discovery Commands

Run these to assess the current state of the test suite. Always run the
staleness check first if resuming from a previous session.

## Active Snapshot (2026-08-26)

The active baseline below was measured from exact current-main HEAD
`d169401398c22b260ceb9d7b8bac539b2c4e0efe` (the worker checkout was
`agent/bu-h4wec.11`; this commit matched `origin/main` at capture time). The
static counts cover both `tests/` and `roster/`, using the async-aware anchored
pattern; the collection count covers `tests/` plus `roster/` with the exact
CI-selection marker and ignore filter.

- **Static functions:** **12,838** under `tests/` plus **4,097** under
  `roster/` (**16,935** combined).
- **CI-selection collection:** **20,024 total**, **15,884 selected**, and
  **4,140 deselected**.

## Historical 2026-08-24 Observations

The earlier same-date measurements remain here as historical observations, not
active anchors: the original staleness note recorded **12,195** static test
functions under `tests/`, while the domains snapshot recorded **12,322** under
`tests/`, **3,956** under `roster/`, and **19,270 total / 15,267 selected** in
the CI-selection collection. They were taken from different checkpoints and
must not be silently conflated.

## Staleness Check (Run First on Resume)

```bash
# Compare actual count against the dated current-cycle baseline.
# Phase 1 (bu-rhztl) baseline 2026-04-05: 13,675 tests; closed at 2,196.
# Phase 2 (bu-hg8rl) baseline 2026-05-03: 3,704; CLOSED 2026-05-05.
# Historical Phase 3 snapshot 2026-06-21: 7,494 def-test funcs / 8,107 collected, 657 files.
# Active snapshot 2026-08-26: tests/ 12,838; roster/ 4,097; combined 16,935.
# Historical 2026-08-24 observations are recorded above; do not use them as
# the active anchor.
# The explicit optional `async` keeps both `def test_` and `async def test_`.
TEST_DEF_PATTERN='^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+test_'
TESTS_CURRENT=$(grep -rEc "$TEST_DEF_PATTERN" tests/ --include='*.py' | awk -F: '{sum+=$2} END {print sum+0}')
ROSTER_CURRENT=$(grep -rEc "$TEST_DEF_PATTERN" roster/ --include='*.py' | awk -F: '{sum+=$2} END {print sum+0}')
ACTIVE_COMBINED_BASELINE=16935
COMBINED_CURRENT=$((TESTS_CURRENT + ROSTER_CURRENT))
COMBINED_DELTA=$((COMBINED_CURRENT - ACTIVE_COMBINED_BASELINE))
COMBINED_DELTA_PERCENT=$(awk -v delta="$COMBINED_DELTA" -v baseline="$ACTIVE_COMBINED_BASELINE" 'BEGIN {printf "%.2f", (delta / baseline) * 100}')
echo "Current static functions: tests/ $TESTS_CURRENT | roster/ $ROSTER_CURRENT | combined $COMBINED_CURRENT | baseline $ACTIVE_COMBINED_BASELINE | delta $COMBINED_DELTA (${COMBINED_DELTA_PERCENT}%) | active snapshot HEAD d169401398c22b260ceb9d7b8bac539b2c4e0efe"

# CI-selection collection (population only, not test-execution timing):
uv run --no-sync pytest tests/ roster/ --collect-only -q \
  --ignore=tests/e2e \
  -m "not integration and not e2e and not nightly and not bench and not perf" \
  2>/dev/null | tail -1   # clamp: without this it prints ~15k lines of stdout

# Check which beads are open in the active maintenance epic
bd list --status all 2>/dev/null | grep -i 'test.*condens\|test.*phase'

# If delta > 10%, update domains.md targets before starting work
```

## Global Assessment

```bash
# Total test count
find tests/ -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print "Total tests:", sum}'

# Tests per directory (sorted)
for dir in tests/*/; do
  count=$(find "$dir" -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print sum}')
  files=$(find "$dir" -name '*.py' | wc -l)
  echo "$count tests in $files files — $(basename $dir)"
done | sort -rn

# Bloated files (>50 tests)
find tests/ -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '$2>50' | sort -t: -k2 -nr

# Total lines of test code
find tests/ -name '*.py' | xargs wc -l 2>/dev/null | tail -1
```

## Smell Detection

```bash
# Files using mocking
grep -rl 'mock\|patch\|MagicMock\|AsyncMock' tests/ | wc -l

# Error message string assertions (high-priority deletion targets)
grep -rn 'assert.*".*[Ee]rror\|assert.*".*[Ii]nvalid\|assert.*message\|assert.*msg' tests/ --include='*.py' | wc -l

# Mock call assertions — REVIEW targets, NOT auto-delete. Many encode real
# contracts (idempotency, retry cadence, resolver bypass, fact-store boundary).
# Apply the plumbing-vs-contract test in classification.md §1 to each.
grep -rn 'assert_called\|assert_awaited\|assert_not_called\|assert_not_awaited\|call_count\|call_args' tests/ --include='*.py' | wc -l

# Imports of private/underscore functions (refactoring risk)
grep -rn 'from butlers.*import _' tests/ --include='*.py' | wc -l

# Duplicate file pairs (test_x.py + test_x_unit.py)
find tests/ -name '*_unit.py' | while read f; do
  base=$(echo "$f" | sed 's/_unit\.py/.py/')
  [ -f "$base" ] && echo "PAIR: $base + $f"
done

# Duplicate test function names across files (copy-paste smell)
grep -rh 'def test_' tests/ --include='*.py' | sed 's/def //' | sed 's/(.*//' | sort | uniq -c | sort -rn | awk '$1>3' | head -20

# Parametrize decorators (usually fine, just quantify)
grep -r '@pytest.mark.parametrize' tests/ --include='*.py' | wc -l
```

## Test Marker Inventory

```bash
# What markers are used? (helps understand existing test categories)
grep -roh '@pytest.mark\.\w\+' tests/ --include='*.py' | sort | uniq -c | sort -rn
```

**Caveat — contract marker is MODULE-LEVEL.** Contract tests are tagged via a
module-level `pytestmark = pytest.mark.contract`, NOT a per-function
`@pytest.mark.contract` decorator. Grepping the decorator UNDERCOUNTS badly
(found 1 vs 226 actually marked). To count/collect contract tests:
```bash
uv run pytest tests/contracts -m contract --collect-only -q | tail -1
grep -rl 'pytestmark = pytest.mark.contract' tests/ --include='*.py'   # files
```

## Shared-Helper Detection (run BEFORE any DELETE_FILE)

Some test files are imported by other test files. Deleting them reds the suite.

```bash
# List every test module imported by another test module (these are NOT deletable):
grep -rhoE 'from tests\.[a-zA-Z0-9_.]+' tests/ --include='*.py' | sort -u

# Before deleting a specific file, prove nothing imports it:
base=$(basename "$FILE" .py)
grep -rn "import .*\b$base\b\|from .*\b$base\b import" tests/ --include='*.py'
```

Never delete: any `conftest.py`, any `__init__.py`,
`tests/modules/test_module_registry.py`, `tests/modules/test_module_pipeline.py`,
`tests/modules/memory/_test_helpers.py`,
`tests/e2e/{envelopes,scenarios,reporting,scoring,benchmark}.py`.

## Migrations Boilerplate Detection (safe, recurring lever)

48 of 52 migration test files repeat tautological self-referential metadata
tests that assert a migration file's metadata against ITSELF (alembic + the
schema-outcome tests already enforce chain integrity). Safe to delete/parametrize:

```bash
# Self-referential metadata boilerplate (near-zero regression value):
grep -rn 'def test_file_exists\|def test_revision\|def test_down_revision\|def test_upgrade_downgrade_callable' tests/migrations --include='*.py' | wc -l

# The tests with REAL value — keep these:
grep -rn 'async def test_.*_after_migration\|to_regclass\|if_exists\|parity\|idempoten' tests/migrations --include='*.py' | wc -l
```

See [domains.md](domains.md) for the standing parametrize-into-conftest pattern.

## Scoped Assessment (for domain-specific beads)

Replace `TARGET_DIR` with the domain directory.

```bash
TARGET_DIR=tests/modules/memory  # example

# Test count in domain
find "$TARGET_DIR" -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print "Tests:", sum}'

# File inventory (sorted by test count)
find "$TARGET_DIR" -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | sort -t: -k2 -nr

# Assert count per file (high-assert files = high-value pruning targets)
for f in $(find "$TARGET_DIR" -name '*.py'); do
  count=$(grep -c 'assert ' "$f" 2>/dev/null)
  [ "$count" -gt 20 ] && echo "$count asserts — $f"
done | sort -rn

# Imports of internal/private functions (pruning targets)
grep -rn 'from butlers\.' "$TARGET_DIR" --include='*.py' | grep '_[a-z]' | head -30

# Mock-wiring tests in this domain specifically
grep -rn 'assert_called\|assert_awaited\|call_count\|call_args' "$TARGET_DIR" --include='*.py' | wc -l

# Error message assertions in this domain
grep -rn 'assert.*".*[Ee]rror\|assert.*".*[Ii]nvalid' "$TARGET_DIR" --include='*.py' | wc -l
```

## New Module Detection

If new modules were added since the skill was written:

```bash
# List all modules in codebase
ls -1 src/butlers/modules/ | grep -v '__\|\.py$'

# Find test directories for modules not listed in domains.md
# Known modules (as of 2026-04-05): memory, approvals, calendar, contacts,
# email, google_drive, spotify, steam, telegram, whatsapp, self_healing,
# insight_broker, pipeline, metrics, autonomy, conversation_history, home_assistant
# If you see a new module not in this list, run scoped assessment on it
```

## Post-Condensation Verification

```bash
# In a worktree first: a fresh worktree has no .venv. Create a REAL one -- never
# symlink the main repo's venv, whose editable-install .pth hardcodes main's
# src/, so `import butlers` would resolve to main and the run would validate
# main's code (bu-1redj). The root conftest.py refuses to run in that state.
uv sync --dev

# Run scoped suite (PREFER THIS — fast, 12-30s per domain)
uv run --no-sync pytest tests/YOUR_DOMAIN -q --tb=short

# Collection must succeed — this is where shared-helper deletions fail.
uv run --no-sync pytest tests/ --collect-only -q

# Contract tests
uv run --no-sync pytest tests/contracts/ -q --tb=short -m contract

# Mirror the CI `check` job's unit step. Keep this in sync with the `run:` line
# in .github/workflows/ci.yml (search for `--cov-report=json`) — it drifts.
# NOTE: unit / smoke / integration are sequential STEPS of the single `check`
# job, not separate jobs.
uv run --no-sync pytest tests/ roster/ -q --maxfail=1 --tb=short \
  --ignore=tests/e2e \
  -m "not integration and not e2e and not nightly and not bench and not perf"

# Compare before/after (counts sync and async test functions)
TEST_DEF_PATTERN='^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+test_'
grep -rEc "$TEST_DEF_PATTERN" tests/ --include='*.py' | awk -F: '{sum+=$2} END {print "Final count:", sum}'
```
