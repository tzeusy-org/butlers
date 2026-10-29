# Immutable public M1 source: 461e03b32ac3b88e2a92d487432f77a73aa2b829
mkdir -p "$(dirname "$COMBINED_COVERAGE")" "$(dirname "$COMBINED_REPORT")"
for coverage_file in \
  $UNIT_1_COVERAGE \
  $UNIT_2_COVERAGE \
  $UNIT_3_COVERAGE \
  $UNIT_4_COVERAGE \
  $UNIT_5_COVERAGE \
  $INTEGRATION_1_COVERAGE \
  $INTEGRATION_2_COVERAGE \
  $INTEGRATION_3_COVERAGE \
  $INTEGRATION_4_COVERAGE \
  $INTEGRATION_5_COVERAGE; do
  test -s "$coverage_file"
done
uv run coverage combine --data-file="$COMBINED_COVERAGE" \
  $UNIT_1_COVERAGE \
  $UNIT_2_COVERAGE \
  $UNIT_3_COVERAGE \
  $UNIT_4_COVERAGE \
  $UNIT_5_COVERAGE \
  $INTEGRATION_1_COVERAGE \
  $INTEGRATION_2_COVERAGE \
  $INTEGRATION_3_COVERAGE \
  $INTEGRATION_4_COVERAGE \
  $INTEGRATION_5_COVERAGE
uv run coverage json --data-file="$COMBINED_COVERAGE" -o "$COMBINED_REPORT"
uv run coverage report --data-file="$COMBINED_COVERAGE" --show-missing
export COMBINED_REPORT
COVERAGE=$(python3 -c 'import json, os; print(round(json.load(open(os.environ["COMBINED_REPORT"]))["totals"]["percent_covered"]))')
echo "percentage=$COVERAGE" >> "$GITHUB_OUTPUT"
# Color thresholds: green >=80, yellow >=60, red <60.
if [ "$COVERAGE" -ge 80 ]; then
  echo "color=brightgreen" >> "$GITHUB_OUTPUT"
elif [ "$COVERAGE" -ge 60 ]; then
  echo "color=yellow" >> "$GITHUB_OUTPUT"
else
  echo "color=red" >> "$GITHUB_OUTPUT"
fi
