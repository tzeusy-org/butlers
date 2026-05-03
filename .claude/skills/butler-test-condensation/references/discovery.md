# Discovery Commands

Run these to assess the current state of the test suite. Always run the
staleness check first if resuming from a previous session.

## Staleness Check (Run First on Resume)

```bash
# Compare actual count against current skill baseline.
# Phase 1 (bu-rhztl) baseline 2026-04-05: 13,675 tests; closed at 2,196.
# Phase 2 baseline 2026-05-03: 3,704 tests across 416 files.
CURRENT=$(find tests/ -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print sum}')
echo "Current: $CURRENT | Phase 2 baseline (2026-05-03): 3704 | Delta: $((CURRENT - 3704))"

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

# Mock call assertions (pure mock-wiring tests — highest-priority deletion)
grep -rn 'assert_called\|assert_awaited\|call_count\|call_args' tests/ --include='*.py' | wc -l

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
# Run scoped suite (PREFER THIS — fast, 12-30s per domain)
uv run pytest tests/YOUR_DOMAIN -q --tb=short

# Run contract tests only (after Phase 1)
uv run pytest tests/contracts/ -q --tb=short -m contract

# Run full suite ONLY for final pre-merge validation (5+ minutes)
uv run pytest tests/ -q --maxfail=3 --tb=short

# Compare before/after
find tests/ -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print "Final count:", sum}'
```
